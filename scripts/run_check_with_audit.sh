#!/usr/bin/env bash
set -uo pipefail

CHECK_NAME="${1:-}"
ENV_FILE="${2:-}"
RUN_CHECK_REQUIRE_AUDIT="${RUN_CHECK_REQUIRE_AUDIT:-true}"

if [[ -z "$CHECK_NAME" || -z "$ENV_FILE" ]]; then
  echo "usage: run_check_with_audit.sh CHECK_NAME ENV_FILE COMMAND [ARGS...]" >&2
  exit 2
fi
shift 2

if [[ "$#" -eq 0 ]]; then
  echo "run_check_with_audit.sh requires a command to execute" >&2
  exit 2
fi

if [[ ! "$CHECK_NAME" =~ ^[A-Za-z0-9_:-]+$ ]]; then
  echo "CHECK_NAME contains unsupported characters" >&2
  exit 2
fi

case "${RUN_CHECK_REQUIRE_AUDIT,,}" in
  true|false) ;;
  *)
    echo "RUN_CHECK_REQUIRE_AUDIT must be true or false" >&2
    exit 2
    ;;
esac

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

output_file="$(mktemp)"
cleanup() {
  rm -f "$output_file"
}
trap cleanup EXIT

"$@" > "$output_file" 2>&1
rc=$?
cat "$output_file"

status="failed"
case "$rc" in
  0)
    if grep -Eqi "^(paper readiness check skipped|paper activity check skipped|paper activity skipped:|paper proof status check skipped:)" "$output_file"; then
      status="skipped"
    else
      status="passed"
    fi
    ;;
  43)
    status="pending"
    ;;
esac

output_tail="$(tail -c 4000 "$output_file" 2>/dev/null || true)"
context_line="$(grep -E '^scheduled check context: ' "$output_file" | tail -n 1 || true)"
proof_summary_line="$(grep -E '^paper proof summary: ' "$output_file" | tail -n 1 || true)"
proof_nightly_automation_line="$(grep -E '^paper proof nightly automation: ' "$output_file" | tail -n 1 || true)"
proof_progress_line="$(grep -E '^paper proof progress: ' "$output_file" | tail -n 1 || true)"
proof_blocker_gaps_line="$(grep -E '^paper proof blocker gaps: ' "$output_file" | tail -n 1 || true)"
proof_active_day_detail_line="$(grep -E '^paper proof active day detail: ' "$output_file" | tail -n 1 || true)"
proof_concentration_line="$(grep -E '^paper proof concentration: ' "$output_file" | tail -n 1 || true)"
proof_strategy_diversification_line="$(grep -E '^paper proof strategy diversification: ' "$output_file" | tail -n 1 || true)"
proof_second_strategy_promotion_action_line="$(grep -E '^paper proof second strategy promotion action: ' "$output_file" | tail -n 1 || true)"
proof_second_strategy_approval_quick_command_line="$(grep -E '^paper proof second strategy approval quick command: ' "$output_file" | tail -n 1 || true)"
proof_scoring_line="$(grep -E '^paper proof scoring: ' "$output_file" | tail -n 1 || true)"
proof_scenarios_line="$(grep -E '^paper proof scenarios: ' "$output_file" | tail -n 1 || true)"
proof_execution_quality_line="$(grep -E '^paper proof execution quality: ' "$output_file" | tail -n 1 || true)"
proof_current_execution_line="$(grep -E '^paper proof current-session execution: ' "$output_file" | tail -n 1 || true)"
proof_post_supervisor_execution_line="$(grep -E '^paper proof post-supervisor execution: ' "$output_file" | tail -n 1 || true)"
decision_dry_run_line="$(grep -E '^paper decision dry run ok: ' "$output_file" | tail -n 1 || true)"
decision_dry_run_strategies_line="$(grep -E '^paper readiness decision dry run strategies ok: ' "$output_file" | tail -n 1 || true)"

if [[ "$CHECK_NAME" == "paper_proof_status" && "$status" == "passed" ]]; then
  case "$proof_summary_line" in
    *" proof=pending "*|*" proof=pending")
      status="pending"
      ;;
  esac
fi

export AUDIT_CHECK_NAME="$CHECK_NAME"
export AUDIT_STATUS="$status"
export AUDIT_EXIT_CODE="$rc"
export AUDIT_OUTPUT_TAIL="$output_tail"
export AUDIT_CONTEXT_LINE="$context_line"
export AUDIT_PROOF_SUMMARY_LINE="$proof_summary_line"
export AUDIT_PROOF_NIGHTLY_AUTOMATION_LINE="$proof_nightly_automation_line"
export AUDIT_PROOF_PROGRESS_LINE="$proof_progress_line"
export AUDIT_PROOF_BLOCKER_GAPS_LINE="$proof_blocker_gaps_line"
export AUDIT_PROOF_ACTIVE_DAY_DETAIL_LINE="$proof_active_day_detail_line"
export AUDIT_PROOF_CONCENTRATION_LINE="$proof_concentration_line"
export AUDIT_PROOF_STRATEGY_DIVERSIFICATION_LINE="$proof_strategy_diversification_line"
export AUDIT_PROOF_SECOND_STRATEGY_PROMOTION_ACTION_LINE="$proof_second_strategy_promotion_action_line"
export AUDIT_PROOF_SECOND_STRATEGY_APPROVAL_QUICK_COMMAND_LINE="$proof_second_strategy_approval_quick_command_line"
export AUDIT_PROOF_SCORING_LINE="$proof_scoring_line"
export AUDIT_PROOF_SCENARIOS_LINE="$proof_scenarios_line"
export AUDIT_PROOF_EXECUTION_QUALITY_LINE="$proof_execution_quality_line"
export AUDIT_PROOF_CURRENT_EXECUTION_LINE="$proof_current_execution_line"
export AUDIT_PROOF_POST_SUPERVISOR_EXECUTION_LINE="$proof_post_supervisor_execution_line"
export AUDIT_DECISION_DRY_RUN_LINE="$decision_dry_run_line"
export AUDIT_DECISION_DRY_RUN_STRATEGIES_LINE="$decision_dry_run_strategies_line"

audit_failed=false
if ! docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e AUDIT_CHECK_NAME \
    -e AUDIT_STATUS \
    -e AUDIT_EXIT_CODE \
    -e AUDIT_OUTPUT_TAIL \
    -e AUDIT_CONTEXT_LINE \
    -e AUDIT_PROOF_SUMMARY_LINE \
    -e AUDIT_PROOF_NIGHTLY_AUTOMATION_LINE \
    -e AUDIT_PROOF_PROGRESS_LINE \
    -e AUDIT_PROOF_BLOCKER_GAPS_LINE \
    -e AUDIT_PROOF_ACTIVE_DAY_DETAIL_LINE \
    -e AUDIT_PROOF_CONCENTRATION_LINE \
    -e AUDIT_PROOF_STRATEGY_DIVERSIFICATION_LINE \
    -e AUDIT_PROOF_SECOND_STRATEGY_PROMOTION_ACTION_LINE \
    -e AUDIT_PROOF_SECOND_STRATEGY_APPROVAL_QUICK_COMMAND_LINE \
    -e AUDIT_PROOF_SCORING_LINE \
    -e AUDIT_PROOF_SCENARIOS_LINE \
    -e AUDIT_PROOF_EXECUTION_QUALITY_LINE \
    -e AUDIT_PROOF_CURRENT_EXECUTION_LINE \
    -e AUDIT_PROOF_POST_SUPERVISOR_EXECUTION_LINE \
    -e AUDIT_DECISION_DRY_RUN_LINE \
    -e AUDIT_DECISION_DRY_RUN_STRATEGIES_LINE \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import shlex

from alpaca_bot.config import Settings
from alpaca_bot.storage import AuditEvent, AuditEventStore
from alpaca_bot.storage.db import connect_postgres

CONTEXT_PREFIX = "scheduled check context: "
CONTEXT_KEYS = {
    "session_date",
    "previous_session_date",
    "proof_start",
    "reason",
    "strategy",
    "strategies",
    "min_trades",
    "min_pnl",
    "session_guard_min_trades",
    "session_guard_min_pnl",
}
CONTEXT_VALUE = re.compile(r"^[A-Za-z0-9_.:,+-]+$")
PROOF_VALUE = re.compile(r"^[A-Za-z0-9_.:,+/;=@-]+$")
PROOF_COMMAND_VALUE = re.compile(r"^[A-Za-z0-9_.:,+/;=@ -]+$")
PROOF_SUMMARY_PREFIX = "paper proof summary: "
PROOF_NIGHTLY_AUTOMATION_PREFIX = "paper proof nightly automation: "
PROOF_PROGRESS_PREFIX = "paper proof progress: "
PROOF_BLOCKER_GAPS_PREFIX = "paper proof blocker gaps: "
PROOF_ACTIVE_DAY_DETAIL_PREFIX = "paper proof active day detail: "
PROOF_CONCENTRATION_PREFIX = "paper proof concentration: "
PROOF_STRATEGY_DIVERSIFICATION_PREFIX = "paper proof strategy diversification: "
PROOF_SECOND_STRATEGY_PROMOTION_ACTION_PREFIX = (
    "paper proof second strategy promotion action: "
)
PROOF_SECOND_STRATEGY_APPROVAL_QUICK_COMMAND_PREFIX = (
    "paper proof second strategy approval quick command: "
)
PROOF_SCORING_PREFIX = "paper proof scoring: "
PROOF_SCENARIOS_PREFIX = "paper proof scenarios: "
PROOF_EXECUTION_QUALITY_PREFIX = "paper proof execution quality: "
PROOF_CURRENT_EXECUTION_PREFIX = "paper proof current-session execution: "
PROOF_POST_SUPERVISOR_EXECUTION_PREFIX = "paper proof post-supervisor execution: "
DECISION_DRY_RUN_PREFIX = "paper decision dry run ok: "
DECISION_DRY_RUN_STRATEGIES_PREFIX = "paper readiness decision dry run strategies ok: "
PROOF_SUMMARY_FIELDS = {
    "readiness": "proof_readiness",
    "proof": "proof_status",
    "reason": "proof_reason",
    "overall_reason": "proof_overall_reason",
    "blockers": "proof_blockers",
    "evidence_blockers": "proof_evidence_blockers",
    "sealed_evidence_blockers": "proof_sealed_evidence_blockers",
    "overall_blockers": "proof_overall_blockers",
    "clean_window_blockers": "proof_clean_window_blockers",
    "sealed_clean_window_blockers": "proof_sealed_clean_window_blockers",
    "warnings": "proof_warnings",
}
PROOF_NIGHTLY_AUTOMATION_FIELDS = {
    "status": "proof_nightly_status",
    "lock_status": "proof_nightly_lock_status",
    "pid": "proof_nightly_pid",
    "source": "proof_nightly_source",
    "age_minutes": "proof_nightly_age_minutes",
    "log_age_minutes": "proof_nightly_log_age_minutes",
    "active_log": "proof_nightly_active_log",
    "max_age_minutes": "proof_nightly_max_age_minutes",
    "stall_minutes": "proof_nightly_stall_minutes",
    "run_age_limit_status": "proof_nightly_run_age_limit_status",
    "log_stall_status": "proof_nightly_log_stall_status",
    "stage": "proof_nightly_stage",
    "second_strategy_scan_status": "proof_second_strategy_scan_status",
    "second_strategy_scan_detail": "proof_second_strategy_scan_detail",
    "detail": "proof_nightly_detail",
}
PROOF_PROGRESS_FIELDS = {
    "status": "proof_progress_status",
    "closed_trades": "proof_closed_trades",
    "required_trades": "proof_required_trades",
    "pnl": "proof_pnl",
    "required_pnl": "proof_required_pnl",
    "first_exit_session": "proof_first_exit_session",
    "latest_exit_session": "proof_latest_exit_session",
}
PROOF_BLOCKER_GAPS_FIELDS = {
    "sample_trades_remaining": "proof_gap_sample_trades_remaining",
    "active_days_remaining": "proof_gap_active_days_remaining",
    "approved_replay_strategy_gap": "proof_gap_approved_replay_strategy_gap",
    "concentration_net_pnl_needed": "proof_gap_concentration_net_pnl_needed",
    "concentration_non_best_avg_pnl": "proof_gap_concentration_non_best_avg_pnl",
    "concentration_non_best_avg_trade_gap": (
        "proof_gap_concentration_non_best_avg_trade_gap"
    ),
    "concentration_runway_status": "proof_gap_concentration_runway_status",
    "concentration_remaining_trade_required_avg_pnl": (
        "proof_gap_concentration_remaining_trade_required_avg_pnl"
    ),
    "concentration_remaining_active_day_required_pnl": (
        "proof_gap_concentration_remaining_active_day_required_pnl"
    ),
    "single_win_pnl_share": "proof_gap_single_win_pnl_share",
    "max_single_win_pnl_share": "proof_gap_max_single_win_pnl_share",
}
PROOF_ACTIVE_DAY_DETAIL_FIELDS = {
    "status": "proof_active_day_status",
    "active_days": "proof_active_days",
    "required_active_days": "proof_required_active_days",
    "active_days_remaining": "proof_active_days_remaining",
    "sample_trades_remaining": "proof_sample_trades_remaining",
    "remaining_trades_per_required_active_day": (
        "proof_remaining_trades_per_required_active_day"
    ),
    "sessions": "proof_active_day_sessions",
    "trades_by_session": "proof_trades_by_session",
    "latest_exit_session": "proof_active_day_latest_exit_session",
    "next_possible_session": "proof_active_day_next_possible_session",
    "future_sessions": "proof_active_day_future_sessions",
    "earliest_active_days_met_session": (
        "proof_earliest_active_days_met_session"
    ),
    "projection_status": "proof_active_day_projection_status",
    "projection_warning": "proof_active_day_projection_warning",
}
PROOF_CONCENTRATION_FIELDS = {
    "status": "proof_concentration_status",
    "best_winning_trade": "proof_concentration_best_winning_trade",
    "best_winning_trade_pnl": "proof_concentration_best_winning_trade_pnl",
    "total_pnl": "proof_concentration_total_pnl",
    "non_best_trades": "proof_concentration_non_best_trades",
    "non_best_pnl": "proof_concentration_non_best_pnl",
    "non_best_avg_pnl": "proof_concentration_non_best_avg_pnl",
    "net_pnl_needed": "proof_concentration_net_pnl_needed",
    "non_best_avg_trade_gap": "proof_concentration_non_best_avg_trade_gap",
    "runway_status": "proof_concentration_runway_status",
    "remaining_trade_required_avg_pnl": (
        "proof_concentration_remaining_trade_required_avg_pnl"
    ),
    "remaining_active_day_required_pnl": (
        "proof_concentration_remaining_active_day_required_pnl"
    ),
    "single_win_pnl_share": "proof_concentration_single_win_pnl_share",
    "max_single_win_pnl_share": "proof_concentration_max_single_win_pnl_share",
}
PROOF_STRATEGY_DIVERSIFICATION_FIELDS = {
    "status": "proof_strategy_diversification_status",
    "active": "proof_strategy_diversification_active",
    "required": "proof_strategy_diversification_required",
    "approved_active": "proof_strategy_diversification_approved_active",
    "approved_replay_active": "proof_strategy_diversification_approved_replay_active",
    "approved_required": "proof_strategy_diversification_approved_required",
    "gap": "proof_strategy_diversification_gap",
    "candidate_status": "proof_strategy_diversification_candidate_status",
    "promotion_action_status": (
        "proof_strategy_diversification_promotion_action_status"
    ),
    "approval_marker_action_status": (
        "proof_strategy_diversification_approval_marker_action_status"
    ),
    "promotion_write_access_status": (
        "proof_strategy_diversification_promotion_write_access_status"
    ),
    "active_names": "proof_strategy_diversification_active_names",
    "approved_names": "proof_strategy_diversification_approved_names",
    "approved_replay_names": "proof_strategy_diversification_approved_replay_names",
    "validated_unapproved_stock_candidates": (
        "proof_strategy_diversification_validated_unapproved_stock_candidates"
    ),
    "validated_unapproved_option_candidates": (
        "proof_strategy_diversification_validated_unapproved_option_candidates"
    ),
}
PROOF_SECOND_STRATEGY_PROMOTION_ACTION_FIELDS = {
    "status": "proof_second_strategy_promotion_action_status",
    "strategy": "proof_second_strategy_promotion_action_strategy",
    "confirmation": "proof_second_strategy_promotion_action_confirmation",
    "approval_marker_action_status": (
        "proof_second_strategy_promotion_action_approval_marker_action_status"
    ),
    "approval_marker_command_status": (
        "proof_second_strategy_promotion_action_approval_marker_command_status"
    ),
    "approval_marker_command_script": (
        "proof_second_strategy_promotion_action_approval_marker_command_script"
    ),
    "approval_marker_command_confirm_env": (
        "proof_second_strategy_promotion_action_approval_marker_command_confirm_env"
    ),
    "approval_marker_command_dry_run_env": (
        "proof_second_strategy_promotion_action_approval_marker_command_dry_run_env"
    ),
    "approval_marker_command_dry_run_value": (
        "proof_second_strategy_promotion_action_approval_marker_command_dry_run_value"
    ),
    "approval_marker_command_approval_only_env": (
        "proof_second_strategy_promotion_action_approval_marker_command_approval_only_env"
    ),
    "approval_marker_command_approval_only_value": (
        "proof_second_strategy_promotion_action_approval_marker_command_approval_only_value"
    ),
    "approval_marker_command_evidence_root": (
        "proof_second_strategy_promotion_action_approval_marker_command_evidence_root"
    ),
    "approval_marker_command_deploy_script": (
        "proof_second_strategy_promotion_action_approval_marker_command_deploy_script"
    ),
    "candidate_decision_dry_run_required": (
        "proof_second_strategy_promotion_action_candidate_decision_dry_run_required"
    ),
    "candidate_decision_dry_run_allow_disabled": (
        "proof_second_strategy_promotion_action_candidate_decision_dry_run_allow_disabled"
    ),
    "candidate_decision_dry_run_script": (
        "proof_second_strategy_promotion_action_candidate_decision_dry_run_script"
    ),
    "approval_marker_overlay_status": (
        "proof_second_strategy_promotion_action_approval_marker_overlay_status"
    ),
    "approval_marker_overlay_marker": (
        "proof_second_strategy_promotion_action_approval_marker_overlay_marker"
    ),
    "approval_marker_overlay_env_file": (
        "proof_second_strategy_promotion_action_approval_marker_overlay_env_file"
    ),
    "broker_flat_status": (
        "proof_second_strategy_promotion_action_broker_flat_status"
    ),
    "env_file": "proof_second_strategy_promotion_action_env_file",
    "write_access_status": (
        "proof_second_strategy_promotion_action_write_access_status"
    ),
    "promotion_handoff_status": (
        "proof_second_strategy_promotion_action_handoff_status"
    ),
    "promotion_handoff_step": (
        "proof_second_strategy_promotion_action_handoff_step"
    ),
    "promotion_env_keys": (
        "proof_second_strategy_promotion_action_env_keys"
    ),
    "env_file_writable": (
        "proof_second_strategy_promotion_action_env_file_writable"
    ),
    "env_dir_writable": (
        "proof_second_strategy_promotion_action_env_dir_writable"
    ),
    "approval_marker": (
        "proof_second_strategy_promotion_action_approval_marker"
    ),
    "approval_marker_writable": (
        "proof_second_strategy_promotion_action_approval_marker_writable"
    ),
    "approval_marker_dir_writable": (
        "proof_second_strategy_promotion_action_approval_marker_dir_writable"
    ),
    "approval_marker_status": (
        "proof_second_strategy_promotion_action_approval_marker_status"
    ),
    "validation_summary": (
        "proof_second_strategy_promotion_action_validation_summary"
    ),
    "validation_summary_sha256": (
        "proof_second_strategy_promotion_action_validation_summary_sha256"
    ),
    "candidate_scale": (
        "proof_second_strategy_promotion_action_candidate_scale"
    ),
    "candidate_trades": (
        "proof_second_strategy_promotion_action_candidate_trades"
    ),
    "candidate_total_pnl": (
        "proof_second_strategy_promotion_action_candidate_total_pnl"
    ),
    "candidate_ci_low": (
        "proof_second_strategy_promotion_action_candidate_ci_low"
    ),
    "candidate_p_mean_le_zero": (
        "proof_second_strategy_promotion_action_candidate_p_mean_le_zero"
    ),
}
PROOF_SCORING_FIELDS = {
    "scoreable_closed_trades": "proof_scoreable_closed_trades",
    "unpaired_filled_exits": "proof_unpaired_filled_exits",
    "unpaired_symbols": "proof_unpaired_symbols",
}
PROOF_SCENARIOS_FIELDS = {
    "status": "proof_scenario_status",
    "active": "proof_scenario_active",
    "expected_session": "proof_scenario_expected_session",
    "problems": "proof_scenario_problems",
}
PROOF_EXECUTION_QUALITY_FIELDS = {
    "status": "proof_execution_quality_status",
    "warnings": "proof_execution_quality_warnings",
    "evaluated": "proof_execution_quality_evaluated",
    "signals": "proof_execution_quality_signals",
    "accepted": "proof_execution_quality_accepted",
    "accepted_for_fill": "proof_execution_quality_accepted_for_fill",
    "capacity_rejected": "proof_execution_quality_capacity_rejected",
    "capacity_reject_rate": "proof_execution_quality_capacity_reject_rate",
    "max_capacity_reject_rate": "proof_execution_quality_max_capacity_reject_rate",
    "entry_quality_rejected": "proof_execution_quality_entry_quality_rejected",
    "vwap_rejected": "proof_execution_quality_vwap_rejected",
    "sizing_rejected": "proof_execution_quality_sizing_rejected",
    "entry_orders": "proof_execution_quality_entry_orders",
    "filled": "proof_execution_quality_filled",
    "canceled": "proof_execution_quality_canceled",
    "expired": "proof_execution_quality_expired",
    "rejected": "proof_execution_quality_rejected",
    "active": "proof_execution_quality_active",
    "maintenance_drained": "proof_execution_quality_maintenance_drained",
    "short_window_drained": "proof_execution_quality_short_window_drained",
    "entry_fill_rate_status": "proof_execution_quality_entry_fill_rate_status",
    "entry_fill_rate": "proof_execution_quality_entry_fill_rate",
    "min_entry_fill_rate": "proof_execution_quality_min_entry_fill_rate",
    "current_posture_entry_orders": (
        "proof_execution_quality_current_posture_entry_orders"
    ),
    "current_posture_filled": "proof_execution_quality_current_posture_filled",
    "current_posture_entry_fill_rate": (
        "proof_execution_quality_current_posture_entry_fill_rate"
    ),
    "current_posture_would_reject": (
        "proof_execution_quality_current_posture_would_reject"
    ),
    "effective_entry_fill_rate": "proof_execution_quality_effective_entry_fill_rate",
    "effective_entry_fill_rate_source": (
        "proof_execution_quality_effective_entry_fill_rate_source"
    ),
    "accepted_to_fill_rate": "proof_execution_quality_accepted_to_fill_rate",
    "filled_symbols": "proof_execution_quality_filled_symbols",
    "expired_symbols": "proof_execution_quality_expired_symbols",
    "expired_reasons": "proof_execution_quality_expired_reasons",
    "expired_signal_price_posture": (
        "proof_execution_quality_expired_signal_price_posture"
    ),
    "expired_next_bar_fill_causes": (
        "proof_execution_quality_expired_next_bar_fill_causes"
    ),
    "entry_dispatch_delay": "proof_execution_quality_entry_dispatch_delay",
    "current_posture_entry_dispatch_delay": (
        "proof_execution_quality_current_posture_entry_dispatch_delay"
    ),
    "current_posture_filled_symbols": (
        "proof_execution_quality_current_posture_filled_symbols"
    ),
}
PROOF_CURRENT_EXECUTION_FIELDS = {
    "session": "proof_current_execution_session",
    "status": "proof_current_execution_status",
    "warnings": "proof_current_execution_warnings",
    "evaluated": "proof_current_execution_evaluated",
    "signals": "proof_current_execution_signals",
    "accepted": "proof_current_execution_accepted",
    "accepted_for_fill": "proof_current_execution_accepted_for_fill",
    "settled_accepted_for_fill": "proof_current_execution_settled_accepted_for_fill",
    "capacity_rejected": "proof_current_execution_capacity_rejected",
    "capacity_reject_rate": "proof_current_execution_capacity_reject_rate",
    "max_capacity_reject_rate": "proof_current_execution_max_capacity_reject_rate",
    "entry_orders": "proof_current_execution_entry_orders",
    "settled": "proof_current_execution_settled_entries",
    "settled_filled": "proof_current_execution_settled_filled",
    "filled": "proof_current_execution_filled",
    "canceled": "proof_current_execution_canceled",
    "expired": "proof_current_execution_expired",
    "rejected": "proof_current_execution_rejected",
    "active": "proof_current_execution_active",
    "maintenance_drained": "proof_current_execution_maintenance_drained",
    "short_window_drained": "proof_current_execution_short_window_drained",
    "settled_entry_fill_rate": "proof_current_execution_settled_entry_fill_rate",
    "entry_fill_rate": "proof_current_execution_entry_fill_rate",
    "min_entry_fill_rate": "proof_current_execution_min_entry_fill_rate",
    "accepted_to_fill_rate": "proof_current_execution_accepted_to_fill_rate",
    "filled_symbols": "proof_current_execution_filled_symbols",
    "expired_symbols": "proof_current_execution_expired_symbols",
    "expired_reasons": "proof_current_execution_expired_reasons",
    "expired_signal_price_posture": (
        "proof_current_execution_expired_signal_price_posture"
    ),
    "expired_next_bar_fill_causes": (
        "proof_current_execution_expired_next_bar_fill_causes"
    ),
    "entry_dispatch_delay": "proof_current_execution_entry_dispatch_delay",
    "active_symbols": "proof_current_execution_active_symbols",
    "maintenance_drained_symbols": "proof_current_execution_maintenance_drained_symbols",
    "short_window_drained_symbols": "proof_current_execution_short_window_drained_symbols",
    "short_window": "proof_current_execution_short_window",
    "min_remaining_active_minutes": "proof_current_execution_min_remaining_active_minutes",
    "short_window_symbols": "proof_current_execution_short_window_symbols",
}
PROOF_POST_SUPERVISOR_EXECUTION_FIELDS = {
    "session": "proof_post_supervisor_execution_session",
    "since": "proof_post_supervisor_execution_since",
    "status": "proof_post_supervisor_execution_status",
    "warnings": "proof_post_supervisor_execution_warnings",
    "evaluated": "proof_post_supervisor_execution_evaluated",
    "signals": "proof_post_supervisor_execution_signals",
    "accepted": "proof_post_supervisor_execution_accepted",
    "accepted_for_fill": "proof_post_supervisor_execution_accepted_for_fill",
    "settled_accepted_for_fill": (
        "proof_post_supervisor_execution_settled_accepted_for_fill"
    ),
    "capacity_rejected": "proof_post_supervisor_execution_capacity_rejected",
    "capacity_reject_rate": (
        "proof_post_supervisor_execution_capacity_reject_rate"
    ),
    "max_capacity_reject_rate": (
        "proof_post_supervisor_execution_max_capacity_reject_rate"
    ),
    "entry_orders": "proof_post_supervisor_execution_entry_orders",
    "settled": "proof_post_supervisor_execution_settled_entries",
    "settled_filled": "proof_post_supervisor_execution_settled_filled",
    "filled": "proof_post_supervisor_execution_filled",
    "expired": "proof_post_supervisor_execution_expired",
    "active": "proof_post_supervisor_execution_active",
    "maintenance_drained": (
        "proof_post_supervisor_execution_maintenance_drained"
    ),
    "short_window_drained": (
        "proof_post_supervisor_execution_short_window_drained"
    ),
    "settled_entry_fill_rate": (
        "proof_post_supervisor_execution_settled_entry_fill_rate"
    ),
    "entry_fill_rate": "proof_post_supervisor_execution_entry_fill_rate",
    "min_entry_fill_rate": "proof_post_supervisor_execution_min_entry_fill_rate",
    "accepted_to_fill_rate": (
        "proof_post_supervisor_execution_accepted_to_fill_rate"
    ),
    "filled_symbols": "proof_post_supervisor_execution_filled_symbols",
    "expired_symbols": "proof_post_supervisor_execution_expired_symbols",
    "expired_reasons": "proof_post_supervisor_execution_expired_reasons",
    "expired_signal_price_posture": (
        "proof_post_supervisor_execution_expired_signal_price_posture"
    ),
    "expired_next_bar_fill_causes": (
        "proof_post_supervisor_execution_expired_next_bar_fill_causes"
    ),
    "entry_dispatch_delay": (
        "proof_post_supervisor_execution_entry_dispatch_delay"
    ),
    "active_symbols": "proof_post_supervisor_execution_active_symbols",
    "short_window": "proof_post_supervisor_execution_short_window",
    "min_remaining_active_minutes": (
        "proof_post_supervisor_execution_min_remaining_active_minutes"
    ),
    "short_window_symbols": (
        "proof_post_supervisor_execution_short_window_symbols"
    ),
}
DECISION_DRY_RUN_FIELDS = {
    "strategy": "decision_dry_run_strategy",
    "strategy_disabled": "decision_dry_run_strategy_disabled",
    "allow_disabled": "decision_dry_run_allow_disabled",
    "as_of": "decision_dry_run_as_of",
    "active": "decision_dry_run_active",
    "ignored": "decision_dry_run_ignored",
    "fractionable": "decision_dry_run_fractionable",
    "intraday": "decision_dry_run_intraday",
    "completed_intraday": "decision_dry_run_completed_intraday",
    "daily": "decision_dry_run_daily",
    "thin_completed_lt20": "decision_dry_run_thin_completed_lt20",
    "decision_records": "decision_dry_run_records",
    "accepted": "decision_dry_run_accepted",
    "rejected": "decision_dry_run_rejected",
    "skipped_no_signal": "decision_dry_run_skipped_no_signal",
    "entry_intents": "decision_dry_run_entry_intents",
    "reject_stages": "decision_dry_run_reject_stages",
    "reject_reasons": "decision_dry_run_reject_reasons",
    "equity": "decision_dry_run_equity",
    "sample": "decision_dry_run_sample",
    "sample_times": "decision_dry_run_sample_times",
    "evaluations": "decision_dry_run_evaluations",
    "min_decision_records": "decision_dry_run_min_decision_records",
    "max_accepted": "decision_dry_run_max_accepted",
    "max_entry_intents": "decision_dry_run_max_entry_intents",
}
DECISION_DRY_RUN_STRATEGIES_FIELDS = {
    "strategies": "decision_dry_run_strategies",
    "count": "decision_dry_run_strategy_count",
}


def parse_context(line: str) -> dict[str, str]:
    if not line.startswith(CONTEXT_PREFIX):
        return {}
    try:
        parts = shlex.split(line[len(CONTEXT_PREFIX):])
    except ValueError:
        return {}

    context: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key in CONTEXT_KEYS and CONTEXT_VALUE.fullmatch(value):
            context[key] = value
    return context


def parse_prefixed_fields(
    line: str, *, prefix: str, field_map: dict[str, str]
) -> dict[str, str]:
    if not line.startswith(prefix):
        return {}
    try:
        parts = shlex.split(line[len(prefix):])
    except ValueError:
        return {}

    fields: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        target_key = field_map.get(key)
        if target_key is not None and PROOF_VALUE.fullmatch(value):
            fields[target_key] = value
    return fields


def parse_approval_quick_command(line: str) -> dict[str, str]:
    if not line.startswith(PROOF_SECOND_STRATEGY_APPROVAL_QUICK_COMMAND_PREFIX):
        return {}

    body = line[len(PROOF_SECOND_STRATEGY_APPROVAL_QUICK_COMMAND_PREFIX):]
    marker = " command="
    if marker not in body:
        return {}

    status_text, command = body.split(marker, 1)
    try:
        status_parts = shlex.split(status_text)
    except ValueError:
        return {}

    fields: dict[str, str] = {}
    for part in status_parts:
        if not part.startswith("status="):
            continue
        status = part.split("=", 1)[1]
        if PROOF_VALUE.fullmatch(status):
            fields[
                "proof_second_strategy_promotion_action_approval_marker_quick_command_status"
            ] = status
        break

    if command and PROOF_COMMAND_VALUE.fullmatch(command):
        fields[
            "proof_second_strategy_promotion_action_approval_marker_quick_command"
        ] = command
    return fields


settings = Settings.from_env()
conn = connect_postgres(settings.database_url)
try:
    payload = {
        "check_name": os.environ["AUDIT_CHECK_NAME"],
        "status": os.environ["AUDIT_STATUS"],
        "exit_code": int(os.environ["AUDIT_EXIT_CODE"]),
        "output_tail": os.environ.get("AUDIT_OUTPUT_TAIL", ""),
        "trading_mode": settings.trading_mode.value,
        "strategy_version": settings.strategy_version,
    }
    payload.update(parse_context(os.environ.get("AUDIT_CONTEXT_LINE", "")))
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_SUMMARY_LINE", ""),
            prefix=PROOF_SUMMARY_PREFIX,
            field_map=PROOF_SUMMARY_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_NIGHTLY_AUTOMATION_LINE", ""),
            prefix=PROOF_NIGHTLY_AUTOMATION_PREFIX,
            field_map=PROOF_NIGHTLY_AUTOMATION_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_PROGRESS_LINE", ""),
            prefix=PROOF_PROGRESS_PREFIX,
            field_map=PROOF_PROGRESS_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_BLOCKER_GAPS_LINE", ""),
            prefix=PROOF_BLOCKER_GAPS_PREFIX,
            field_map=PROOF_BLOCKER_GAPS_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_ACTIVE_DAY_DETAIL_LINE", ""),
            prefix=PROOF_ACTIVE_DAY_DETAIL_PREFIX,
            field_map=PROOF_ACTIVE_DAY_DETAIL_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_CONCENTRATION_LINE", ""),
            prefix=PROOF_CONCENTRATION_PREFIX,
            field_map=PROOF_CONCENTRATION_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_STRATEGY_DIVERSIFICATION_LINE", ""),
            prefix=PROOF_STRATEGY_DIVERSIFICATION_PREFIX,
            field_map=PROOF_STRATEGY_DIVERSIFICATION_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_SECOND_STRATEGY_PROMOTION_ACTION_LINE", ""),
            prefix=PROOF_SECOND_STRATEGY_PROMOTION_ACTION_PREFIX,
            field_map=PROOF_SECOND_STRATEGY_PROMOTION_ACTION_FIELDS,
        )
    )
    payload.update(
        parse_approval_quick_command(
            os.environ.get(
                "AUDIT_PROOF_SECOND_STRATEGY_APPROVAL_QUICK_COMMAND_LINE",
                "",
            )
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_SCORING_LINE", ""),
            prefix=PROOF_SCORING_PREFIX,
            field_map=PROOF_SCORING_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_SCENARIOS_LINE", ""),
            prefix=PROOF_SCENARIOS_PREFIX,
            field_map=PROOF_SCENARIOS_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_EXECUTION_QUALITY_LINE", ""),
            prefix=PROOF_EXECUTION_QUALITY_PREFIX,
            field_map=PROOF_EXECUTION_QUALITY_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_CURRENT_EXECUTION_LINE", ""),
            prefix=PROOF_CURRENT_EXECUTION_PREFIX,
            field_map=PROOF_CURRENT_EXECUTION_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_PROOF_POST_SUPERVISOR_EXECUTION_LINE", ""),
            prefix=PROOF_POST_SUPERVISOR_EXECUTION_PREFIX,
            field_map=PROOF_POST_SUPERVISOR_EXECUTION_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_DECISION_DRY_RUN_LINE", ""),
            prefix=DECISION_DRY_RUN_PREFIX,
            field_map=DECISION_DRY_RUN_FIELDS,
        )
    )
    payload.update(
        parse_prefixed_fields(
            os.environ.get("AUDIT_DECISION_DRY_RUN_STRATEGIES_LINE", ""),
            prefix=DECISION_DRY_RUN_STRATEGIES_PREFIX,
            field_map=DECISION_DRY_RUN_STRATEGIES_FIELDS,
        )
    )

    AuditEventStore(conn).append(
        AuditEvent(
            event_type="scheduled_check_completed",
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
    )
finally:
    close = getattr(conn, "close", None)
    if callable(close):
        close()
PY
then
  echo "scheduled check audit warning: failed to append audit event for $CHECK_NAME" >&2
  audit_failed=true
fi

if [[ "$audit_failed" == "true" && "${RUN_CHECK_REQUIRE_AUDIT,,}" == "true" ]]; then
  echo "scheduled check audit failed: refusing scheduled-check exit without audit evidence" >&2
  exit 47
fi

exit "$rc"
