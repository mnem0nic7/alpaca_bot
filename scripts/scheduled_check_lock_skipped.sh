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

load_latest_readiness_status() {
  local readiness_session_date="$1"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e READINESS_SESSION_DATE="$readiness_session_date" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

import os

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
session_date = os.environ["READINESS_SESSION_DATE"]

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT payload->>'status'
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = 'paper_readiness'
              AND payload->>'session_date' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                session_date,
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        row = cur.fetchone()
finally:
    conn.close()

print(f"paper_readiness_latest_status={row[0] if row else ''}")
PY
)"

  printf '%s\n' "$lookup" \
    | sed -n 's/^paper_readiness_latest_status=//p' \
    | tail -n 1
}

case "$CHECK_NAME" in
  paper_readiness)
    latest_readiness_status="$(load_latest_readiness_status "$session_date")"
    if [[ "$latest_readiness_status" == "passed" ]]; then
      echo "scheduled check context: session_date=$session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy_already_passed"
      echo "paper readiness lock busy after prior pass for session $session_date; not blocking entries"
      exit 0
    fi
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
