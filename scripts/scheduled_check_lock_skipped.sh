#!/usr/bin/env bash
set -euo pipefail

CHECK_NAME="${1:-}"
LOCK_FILE="${2:-}"
ENV_FILE="${3:-/etc/alpaca_bot/alpaca-bot.env}"

if [[ -z "$CHECK_NAME" || -z "$LOCK_FILE" ]]; then
  echo "usage: scheduled_check_lock_skipped.sh CHECK_NAME LOCK_FILE [ENV_FILE]" >&2
  exit 2
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

session_date="$(TZ=America/New_York date +%F)"

case "$CHECK_NAME" in
  paper_readiness)
    echo "scheduled check context: session_date=$session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy"
    ;;
  paper_activity)
    echo "scheduled check context: session_date=$session_date strategy=${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}} reason=lock_busy"
    ;;
  session_guard)
    echo "scheduled check context: session_date=$session_date strategy=${SESSION_GUARD_STRATEGY:-bull_flag} reason=lock_busy"
    ;;
  paper_profit_probe)
    echo "scheduled check context: session_date=$session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} strategy=${PROFIT_PROBE_STRATEGY:-bull_flag} min_trades=${PROFIT_PROBE_MIN_TRADES:-10} min_pnl=${PROFIT_PROBE_MIN_PNL:-0.01} reason=lock_busy"
    ;;
  *)
    echo "scheduled check context: session_date=$session_date reason=lock_busy"
    ;;
esac

echo "scheduled check lock busy: check=$CHECK_NAME lock=$LOCK_FILE" >&2
exit 48
