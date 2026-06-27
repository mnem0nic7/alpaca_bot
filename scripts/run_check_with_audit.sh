#!/usr/bin/env bash
set -uo pipefail

CHECK_NAME="${1:-}"
ENV_FILE="${2:-}"

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
if [[ "$rc" -eq 0 ]]; then
  if grep -qi "skipped" "$output_file"; then
    status="skipped"
  else
    status="passed"
  fi
fi

output_tail="$(tail -c 4000 "$output_file" 2>/dev/null || true)"

export AUDIT_CHECK_NAME="$CHECK_NAME"
export AUDIT_STATUS="$status"
export AUDIT_EXIT_CODE="$rc"
export AUDIT_OUTPUT_TAIL="$output_tail"

if ! docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e AUDIT_CHECK_NAME \
    -e AUDIT_STATUS \
    -e AUDIT_EXIT_CODE \
    -e AUDIT_OUTPUT_TAIL \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
import os

from alpaca_bot.config import Settings
from alpaca_bot.storage import AuditEvent, AuditEventStore
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
conn = connect_postgres(settings.database_url)
try:
    AuditEventStore(conn).append(
        AuditEvent(
            event_type="scheduled_check_completed",
            payload={
                "check_name": os.environ["AUDIT_CHECK_NAME"],
                "status": os.environ["AUDIT_STATUS"],
                "exit_code": int(os.environ["AUDIT_EXIT_CODE"]),
                "output_tail": os.environ.get("AUDIT_OUTPUT_TAIL", ""),
                "trading_mode": settings.trading_mode.value,
                "strategy_version": settings.strategy_version,
            },
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
fi

exit "$rc"
