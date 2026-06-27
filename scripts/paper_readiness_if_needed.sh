#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PAPER_READINESS_CHECK_SCRIPT="${PAPER_READINESS_CHECK_SCRIPT:-./scripts/paper_readiness_check.sh}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper readiness check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

latest_readiness="$("${compose[@]}" run -T --rm \
  --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
market_timezone = ZoneInfo(settings.market_timezone.key)
today = datetime.now(market_timezone).date()
session_date = today
override = os.environ.get("PAPER_READINESS_SESSION_DATE", "")

if override:
    session_date = date.fromisoformat(override)
else:
    calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
        start=today,
        end=today + timedelta(days=10),
    )
    for session in calendar:
        if session.session_date >= today:
            session_date = session.session_date
            break

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              payload->>'status',
              to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
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
                session_date.isoformat(),
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        row = cur.fetchone()
        cur.execute(
            """
            SELECT to_char(MAX(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            FROM audit_events
            WHERE event_type = 'supervisor_started'
            """
        )
        supervisor_row = cur.fetchone()
finally:
    conn.close()

status = row[0] if row else ""
readiness_created_at = row[1] if row else ""
supervisor_started_at = supervisor_row[0] if supervisor_row and supervisor_row[0] else ""
print(
    "paper_readiness_latest_status="
    f"{session_date.isoformat()}|{status}|{readiness_created_at}|{supervisor_started_at}"
)
PY
)"
latest_readiness="$(
  printf '%s\n' "$latest_readiness" \
    | sed -n 's/^paper_readiness_latest_status=//p' \
    | tail -n 1
)"

session_date=""
latest_status=""
readiness_created_at=""
supervisor_started_at=""
IFS='|' read -r session_date latest_status readiness_created_at supervisor_started_at <<< "$latest_readiness"

readiness_is_current=true
if [[ -n "$readiness_created_at" && -n "$supervisor_started_at" && "$readiness_created_at" < "$supervisor_started_at" ]]; then
  readiness_is_current=false
fi

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" && "$readiness_is_current" == "true" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-06-29}"
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=already_passed"
  echo "paper readiness already passed for session $session_date; final retry not rerun"
  exit 0
fi

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" && "$readiness_is_current" == "false" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-06-29}"
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=stale_after_supervisor_start"
  echo "paper readiness prior pass is older than latest supervisor start; rerunning final check"
fi

exec "$PAPER_READINESS_CHECK_SCRIPT" "$ENV_FILE"
