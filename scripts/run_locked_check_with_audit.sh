#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_NAME="${1:-}"
LOCK_FILE="${2:-}"
ENV_FILE="${3:-}"

if [[ -z "$CHECK_NAME" || -z "$LOCK_FILE" || -z "$ENV_FILE" ]]; then
  echo "usage: run_locked_check_with_audit.sh CHECK_NAME LOCK_FILE ENV_FILE COMMAND [ARGS...]" >&2
  exit 2
fi
shift 3

if [[ "$#" -eq 0 ]]; then
  echo "run_locked_check_with_audit.sh requires a command to execute" >&2
  exit 2
fi

if flock -n -E 75 "$LOCK_FILE" \
  "$ROOT_DIR/scripts/run_check_with_audit.sh" "$CHECK_NAME" "$ENV_FILE" "$@"; then
  exit 0
fi

rc="$?"
if [[ "$rc" -eq 75 ]]; then
  "$ROOT_DIR/scripts/run_check_with_audit.sh" \
    "$CHECK_NAME" \
    "$ENV_FILE" \
    "$ROOT_DIR/scripts/scheduled_check_lock_skipped.sh" \
    "$CHECK_NAME" \
    "$LOCK_FILE" \
    "$ENV_FILE"
  exit "$?"
fi

exit "$rc"
