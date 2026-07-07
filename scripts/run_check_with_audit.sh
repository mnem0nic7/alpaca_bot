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
proof_progress_line="$(grep -E '^paper proof progress: ' "$output_file" | tail -n 1 || true)"
proof_scoring_line="$(grep -E '^paper proof scoring: ' "$output_file" | tail -n 1 || true)"
proof_scenarios_line="$(grep -E '^paper proof scenarios: ' "$output_file" | tail -n 1 || true)"
proof_current_execution_line="$(grep -E '^paper proof current-session execution: ' "$output_file" | tail -n 1 || true)"
decision_dry_run_line="$(grep -E '^paper decision dry run ok: ' "$output_file" | tail -n 1 || true)"
decision_dry_run_strategies_line="$(grep -E '^paper readiness decision dry run strategies ok: ' "$output_file" | tail -n 1 || true)"

export AUDIT_CHECK_NAME="$CHECK_NAME"
export AUDIT_STATUS="$status"
export AUDIT_EXIT_CODE="$rc"
export AUDIT_OUTPUT_TAIL="$output_tail"
export AUDIT_CONTEXT_LINE="$context_line"
export AUDIT_PROOF_SUMMARY_LINE="$proof_summary_line"
export AUDIT_PROOF_PROGRESS_LINE="$proof_progress_line"
export AUDIT_PROOF_SCORING_LINE="$proof_scoring_line"
export AUDIT_PROOF_SCENARIOS_LINE="$proof_scenarios_line"
export AUDIT_PROOF_CURRENT_EXECUTION_LINE="$proof_current_execution_line"
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
    -e AUDIT_PROOF_PROGRESS_LINE \
    -e AUDIT_PROOF_SCORING_LINE \
    -e AUDIT_PROOF_SCENARIOS_LINE \
    -e AUDIT_PROOF_CURRENT_EXECUTION_LINE \
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
PROOF_VALUE = re.compile(r"^[A-Za-z0-9_.:,+/;@-]+$")
PROOF_SUMMARY_PREFIX = "paper proof summary: "
PROOF_PROGRESS_PREFIX = "paper proof progress: "
PROOF_SCORING_PREFIX = "paper proof scoring: "
PROOF_SCENARIOS_PREFIX = "paper proof scenarios: "
PROOF_CURRENT_EXECUTION_PREFIX = "paper proof current-session execution: "
DECISION_DRY_RUN_PREFIX = "paper decision dry run ok: "
DECISION_DRY_RUN_STRATEGIES_PREFIX = "paper readiness decision dry run strategies ok: "
PROOF_SUMMARY_FIELDS = {
    "readiness": "proof_readiness",
    "proof": "proof_status",
    "reason": "proof_reason",
    "blockers": "proof_blockers",
    "evidence_blockers": "proof_evidence_blockers",
    "sealed_evidence_blockers": "proof_sealed_evidence_blockers",
    "overall_blockers": "proof_overall_blockers",
    "clean_window_blockers": "proof_clean_window_blockers",
    "sealed_clean_window_blockers": "proof_sealed_clean_window_blockers",
    "warnings": "proof_warnings",
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
    "settled_entry_fill_rate": "proof_current_execution_settled_entry_fill_rate",
    "entry_fill_rate": "proof_current_execution_entry_fill_rate",
    "min_entry_fill_rate": "proof_current_execution_min_entry_fill_rate",
    "accepted_to_fill_rate": "proof_current_execution_accepted_to_fill_rate",
    "filled_symbols": "proof_current_execution_filled_symbols",
    "expired_symbols": "proof_current_execution_expired_symbols",
    "active_symbols": "proof_current_execution_active_symbols",
    "maintenance_drained_symbols": "proof_current_execution_maintenance_drained_symbols",
}
DECISION_DRY_RUN_FIELDS = {
    "strategy": "decision_dry_run_strategy",
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
            os.environ.get("AUDIT_PROOF_PROGRESS_LINE", ""),
            prefix=PROOF_PROGRESS_PREFIX,
            field_map=PROOF_PROGRESS_FIELDS,
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
            os.environ.get("AUDIT_PROOF_CURRENT_EXECUTION_LINE", ""),
            prefix=PROOF_CURRENT_EXECUTION_PREFIX,
            field_map=PROOF_CURRENT_EXECUTION_FIELDS,
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
