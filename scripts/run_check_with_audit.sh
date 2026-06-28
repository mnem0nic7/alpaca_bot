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

"$@" > >(tee "$output_file") 2> >(tee -a "$output_file" >&2)
rc=$?

status="failed"
case "$rc" in
  0)
    if grep -Eqi "^(paper readiness check skipped|paper activity check skipped|paper activity skipped:)" "$output_file"; then
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

export AUDIT_CHECK_NAME="$CHECK_NAME"
export AUDIT_STATUS="$status"
export AUDIT_EXIT_CODE="$rc"
export AUDIT_OUTPUT_TAIL="$output_tail"
export AUDIT_CONTEXT_LINE="$context_line"
export AUDIT_PROOF_SUMMARY_LINE="$proof_summary_line"
export AUDIT_PROOF_PROGRESS_LINE="$proof_progress_line"

audit_failed=false
if ! docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e AUDIT_CHECK_NAME \
    -e AUDIT_STATUS \
    -e AUDIT_EXIT_CODE \
    -e AUDIT_OUTPUT_TAIL \
    -e AUDIT_CONTEXT_LINE \
    -e AUDIT_PROOF_SUMMARY_LINE \
    -e AUDIT_PROOF_PROGRESS_LINE \
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
    "min_trades",
    "min_pnl",
}
CONTEXT_VALUE = re.compile(r"^[A-Za-z0-9_.:+-]+$")
PROOF_VALUE = re.compile(r"^[A-Za-z0-9_.:,+/-]+$")
PROOF_SUMMARY_PREFIX = "paper proof summary: "
PROOF_PROGRESS_PREFIX = "paper proof progress: "
PROOF_SUMMARY_FIELDS = {
    "readiness": "proof_readiness",
    "proof": "proof_status",
    "reason": "proof_reason",
    "blockers": "proof_blockers",
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
